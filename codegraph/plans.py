"""Planned-change layer: an agreed scope recorded as a small versioned YAML file (plans/<name>.yaml) that is
overlaid on the real graph without mutating it, plus deterministic checks of that scope against the graph.

Nothing here guesses. Every finding is derived from indexed edges (file:line + confidence), from the plan file
itself, from read-only snapshot files next to the plan (filed issues, external client call sites), or from an
exact regex over the indexed source tree (labelled `text-match`, never turned into an edge).

  plan mode    (before implementation): references resolve? what does the plan miss? what conflicts?
  verify mode  (after implementation + re-index): do planned nodes/edges now exist, are forbidden paths gone or
               guarded, did modified targets change since the baseline, are requirements met.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path

from . import presets
from . import query as Q
from .core.model import PROPAGATING
from .core.store import GraphStore

PLAN_VERSION = 1
STATUSES = ("draft", "agreed", "in_progress", "implemented", "abandoned")
TOP_KEYS = {"plan_version", "name", "title", "status", "rationale", "issues", "context", "assumptions", "add_nodes",
            "modify", "add_edges", "forbid", "require", "covers", "out_of_scope", "precedents", "notes"}
ITEM_KEYS = {
    "add_nodes": ({"id"}, {"id", "name", "attrs", "rationale", "issues", "file", "near"}),
    "modify": ({"target", "intent"}, {"target", "intent", "rationale", "issues", "role"}),
    "add_edges": ({"from", "kind", "to"}, {"from", "kind", "to", "intent", "rationale", "issues"}),
    "forbid": ({"id", "from", "to"}, {"id", "type", "from", "to", "edge_kind", "when", "rationale", "issues", "guard"}),
    "require": ({"route"}, {"route", "middleware", "rationale", "issues"}),
    "covers": ({"spec"}, {"spec", "note"}),
    "out_of_scope": ({"spec", "reason"}, {"spec", "reason"}),
    "precedents": ({"name", "pattern"}, {"name", "pattern", "within", "for"}),
}
NEW_NODE_KINDS = {"column", "table", "method", "function", "class", "route", "setting", "config", "env", "request_key",
                  "job", "command", "listener", "page", "component", "composable", "store", "property", "admin"}
CODE_KINDS = ("method", "function", "script", "composable", "store", "component", "module", "page")
WRITE_KINDS = ("WRITES_TABLE", "WRITES_COLUMN")
READ_KINDS = ("READS_TABLE", "READS_COLUMN", "MENTIONS_COLUMN")
# class-name prefixes shortened in reports (codegraph/presets/laravel.yaml plans.short_prefixes)
SHORT_PREFIXES = tuple(presets.values("laravel", "plans", "short_prefixes", default=[]))
# where text mentions are scanned when .cg.yaml has no plans.text_mention_dirs (codegraph/presets/laravel.yaml)
TEXT_MENTION_DIRS = tuple(presets.values("laravel", "plans", "text_mention_dirs", default=[]))
IMPLICIT_NEW = ("request_key", "setting", "config", "env")  # leaf keys a planned edge may introduce without add_nodes
PAYLOAD_VERBS = re.compile(r"^(store|update|create|save|upsert|fill|import|sync|make|add|edit|set)", re.I)


# ----------------------------------------------------------------------------------------------- loading
class PlanError(ValueError):
    pass


def plans_dir(root: str | Path | None = None) -> Path:
    return Path(root) if root else Path(__file__).resolve().parents[1] / "plans"


def resolve_plans_dir(flag: str | None, db: str | None = None) -> str | None:
    """--plans-dir, else plans.dir of the .cg.yaml recorded in the graph at `db`, else None (the default plans/)."""
    if flag:
        return flag
    if db and Path(db).is_file():
        from .config import configured_plans_dir
        from .core.store import GraphStore
        g = GraphStore(db)
        try:
            d = configured_plans_dir(g)
        except Exception:  # noqa: BLE001  (not a graph yet)
            d = None
        finally:
            g.db.close()
        return str(d) if d else None
    return None


def mention_dirs(st, repo: str | None) -> tuple[str, ...]:
    """plans.text_mention_dirs of that repo's .cg.yaml, else the preset default."""
    from .config import graph_configs
    for r, cfg, _ in graph_configs(st):
        if r is None or r == repo:          # single-repo graph, or this repo of a combined one
            d = (cfg.get("plans") or {}).get("text_mention_dirs")
            if d:
                return tuple(d)
    return TEXT_MENTION_DIRS


def find_plan(name_or_path: str, root: str | Path | None = None) -> Path:
    p = Path(name_or_path)
    if p.suffix in (".yaml", ".yml") and p.is_file():
        return p
    for cand in (plans_dir(root) / f"{name_or_path}.yaml", plans_dir(root) / f"{name_or_path}.yml"):
        if cand.is_file():
            return cand
    raise PlanError(f"plan not found: {name_or_path} (looked in {plans_dir(root)})")


def list_plans(root: str | Path | None = None) -> list[dict]:
    out = []
    for p in sorted(plans_dir(root).glob("*.y*ml")):
        try:
            d = _yaml(p)
            if "snapshot_version" in d and "plan_version" not in d:
                continue  # findings / clients snapshot next to the plans, not a plan
            out.append({"name": d.get("name"), "title": d.get("title"), "status": d.get("status", "draft"),
                        "plan_version": d.get("plan_version"), "file": str(p), "issues": d.get("issues") or [],
                        "counts": {k: len(d.get(k) or []) for k in ("add_nodes", "modify", "add_edges", "forbid", "require")},
                        "schema_errors": len(validate_schema(d))})
        except Exception as e:  # noqa: BLE001
            out.append({"name": p.stem, "file": str(p), "error": str(e)})
    return out


def _yaml(path: Path) -> dict:
    import yaml
    try:
        d = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise PlanError(f"{path.name}: invalid YAML: {e}") from None
    if not isinstance(d, dict):
        raise PlanError(f"{path}: top level must be a mapping")
    return d


def validate_schema(d: dict) -> list[str]:
    """Structural validation (no graph needed). Returns problems as 'path: message'."""
    errs = []
    if d.get("plan_version") != PLAN_VERSION:
        errs.append(f"plan_version: must be {PLAN_VERSION} (got {d.get('plan_version')!r})")
    if not isinstance(d.get("name"), str) or not re.fullmatch(r"[a-z0-9][a-z0-9._-]*", d.get("name") or ""):
        errs.append("name: required, lowercase slug [a-z0-9._-]")
    if not isinstance(d.get("title"), str) or not d.get("title"):
        errs.append("title: required string")
    if d.get("status", "draft") not in STATUSES:
        errs.append(f"status: one of {STATUSES}")
    for k in d:
        if k not in TOP_KEYS and not str(k).startswith("_"):
            errs.append(f"{k}: unknown key (allowed: {sorted(TOP_KEYS)})")
    for k in ("issues", "assumptions"):
        if k in d and not (isinstance(d[k], list) and all(isinstance(x, str) for x in d[k])):
            errs.append(f"{k}: list of strings")
    if not isinstance(d.get("context") or {}, dict):
        errs.append("context: mapping {findings: path, clients: path|[paths]}")
    for section, (req, allowed) in ITEM_KEYS.items():
        items = d.get(section)
        if items is None:
            continue
        if not isinstance(items, list):
            errs.append(f"{section}: must be a list")
            continue
        for i, it in enumerate(items):
            where = f"{section}[{i}]"
            if not isinstance(it, dict):
                keys = ", ".join(f"{r}: ..." for r in sorted(req))
                errs.append(f"{where}: must be a mapping like {{{keys}}} (got {type(it).__name__} {str(it)[:60]!r})")
                continue
            for r in req:
                if not it.get(r):
                    errs.append(f"{where}.{r}: required")
            for k in it:
                if k not in allowed:
                    errs.append(f"{where}.{k}: unknown key (allowed: {sorted(allowed)})")
            if "issues" in it and not (isinstance(it["issues"], list) and all(isinstance(x, str) for x in it["issues"])):
                errs.append(f"{where}.issues: list of strings")
            if section == "add_nodes" and it.get("id"):
                kind = str(it["id"]).split(":", 1)[0]
                if ":" not in str(it["id"]) or kind not in NEW_NODE_KINDS:
                    errs.append(f"{where}.id: must be '<kind>:<key>' with kind in {sorted(NEW_NODE_KINDS)}")
                elif kind == "column" and not re.fullmatch(r"column:\w+\.\w+", str(it["id"])):
                    errs.append(f"{where}.id: column ids are column:<table>.<column>")
            if section == "add_edges" and it.get("kind") and not re.fullmatch(r"[A-Z][A-Z_]+", str(it["kind"])):
                errs.append(f"{where}.kind: edge kinds are UPPER_SNAKE (e.g. READS_COLUMN)")
            if section == "forbid":
                if it.get("type", "path") not in ("path", "edge"):
                    errs.append(f"{where}.type: path | edge")
                if it.get("type") == "edge" and not it.get("edge_kind"):
                    errs.append(f"{where}.edge_kind: required for type: edge")
                g = it.get("guard")
                if g is not None and not (isinstance(g, dict) and g.get("at") and (g.get("reads") or g.get("calls"))):
                    errs.append(f"{where}.guard: {{at: <spec>, reads|calls: <spec>}}")
            if section == "modify" and it.get("role") not in (None, "guard"):
                errs.append(f"{where}.role: only 'guard' (the access check every reader of the changed table should pass through)")
            if section == "require" and "middleware" in it and not isinstance(it["middleware"], list):
                errs.append(f"{where}.middleware: list of middleware names")
            if section == "precedents":
                try:
                    re.compile(it.get("pattern") or "")
                except re.error as e:
                    errs.append(f"{where}.pattern: bad regex ({e})")
    ids = [it.get("id") for it in d.get("add_nodes") or [] if isinstance(it, dict)]
    dup = {x for x in ids if ids.count(x) > 1}
    if dup:
        errs.append(f"add_nodes: duplicate ids {sorted(dup)}")
    fids = [it.get("id") for it in d.get("forbid") or [] if isinstance(it, dict)]
    if len(set(fids)) != len(fids):
        errs.append("forbid: ids must be unique")
    return errs


def load_plan(name_or_path: str, root: str | Path | None = None) -> dict:
    p = find_plan(name_or_path, root)
    d = _yaml(p)
    d["_file"] = str(p)
    d["_schema_errors"] = validate_schema(d)
    _drop_malformed(d)
    ctx = d.get("context") or {}
    if not isinstance(ctx, dict):
        ctx = d["context"] = {}
    base = p.parent
    d["_findings"] = _load_findings(base / ctx["findings"]) if ctx.get("findings") else None
    cl = ctx.get("clients") or []
    d["_clients"] = [_yaml(base / c) for c in ([cl] if isinstance(cl, str) else cl)]
    return d


def _drop_malformed(d: dict) -> None:
    """Keep only well-formed section items (mappings with their required keys) so a typo in one item is reported as a
    schema error instead of crashing load / validate / check; the schema errors still name every dropped item."""
    for section, (req, _allowed) in ITEM_KEYS.items():
        items = d.get(section)
        if items is None:
            continue
        d[section] = [it for it in items if isinstance(it, dict) and all(it.get(r) for r in req)] \
            if isinstance(items, list) else []


def _load_findings(p: Path) -> dict:
    d = _yaml(p)
    d["_file"] = str(p)
    for f in d.get("findings") or []:
        ev = []
        for e in f.get("evidence") or []:
            m = re.fullmatch(r"(.+?):(\d+)(?:-(\d+))?", str(e))
            if m:
                ev.append((m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))))
        f["_evidence"] = ev
    return d


# ----------------------------------------------------------------------------------------------- helpers
def short(x: str | None) -> str:
    if not x:
        return "?"
    k, _, key = x.partition(":")
    if k in ("method", "class", "function", "interface", "trait", "enum", "property", "admin"):
        key = key.lstrip("\\")
        cls, sep, mem = key.partition("::")
        for pre in SHORT_PREFIXES:
            if cls.startswith(pre):
                cls = cls[len(pre):]
                break
        else:
            cls = cls.split("\\", 1)[1] if cls.startswith("App\\") else cls
        return cls + (sep + mem if sep else "") + ("" if k in ("method", "class", "function") else f" [{k}]")
    return x


def loc(file: str | None, line) -> str:
    return f"{file}:{line}" if file else "?"


def bloc(at: str | None) -> str:
    """basename:line of a 'file:line' string."""
    if not at:
        return "?"
    f, _, ln = at.rpartition(":")
    return f"{f.rsplit('/', 1)[-1]}:{ln}"


def fmt_chain(path: list[dict], limit=7) -> str:
    """Compact call chain: A -KIND@file:line-> B -> ..."""
    if not path:
        return ""
    hops = path if len(path) <= limit else path[:3] + [None] + path[-(limit - 4):]
    s = short(path[0]["from"])
    for h in hops:
        if h is None:
            s += " -> ..."
            continue
        g = " GATED" if h.get("gated") else ""
        s += f" -{h['kind']}@{bloc(h.get('at'))}{g}-> {short(h['to'])}"
    return s


def singular(t: str) -> str:
    if t.endswith("ies"):
        return t[:-3] + "y"
    if t.endswith("ses"):
        return t[:-2]
    return t[:-1] if t.endswith("s") else t


class Ctx:
    """Graph access + source roots for one check run."""

    def __init__(self, st: GraphStore, plan: dict, roots: dict[str, str] | None = None):
        self.st, self.plan = st, plan
        from .viz.graph import Sources
        self.src = Sources(st, roots)
        self.planned_nodes = {it["id"]: it for it in plan.get("add_nodes") or []}
        self.implicit = {str(it[k]) for it in plan.get("add_edges") or [] for k in ("from", "to")
                         if str(it[k]).split(":", 1)[0] in IMPLICIT_NEW}
        self.combined = bool(st.meta().get("sources"))
        self._node: dict = {}
        self._res: dict = {}

    def node(self, nid):
        if nid not in self._node:
            r = self.st.node(nid)
            self._node[nid] = dict(r) if r else None
        return self._node[nid]

    def resolve(self, spec: str) -> dict:
        """-> {spec, ids, status: ok|planned|client|unresolved|ambiguous}. Plan semantics: a class spec means the class
        node (not its methods); Class::method must be unique; table:<t> resolves when the table or its columns exist."""
        spec = str(spec).strip()
        if spec in self._res:
            return self._res[spec]
        self._res[spec] = r = self._resolve(spec)
        return r

    def _resolve(self, spec: str) -> dict:
        if self.st.q("SELECT 1 FROM nodes WHERE id=?", (spec,)):
            return {"spec": spec, "ids": [spec], "status": "ok"}
        if spec in self.planned_nodes:
            return {"spec": spec, "ids": [spec], "status": "planned"}
        if spec.split(":", 1)[0] in IMPLICIT_NEW and spec in self.implicit:
            return {"spec": spec, "ids": [spec], "status": "planned"}  # new request key / setting named by a planned edge
        if spec.startswith("client:"):
            return {"spec": spec, "ids": [spec], "status": "client"}
        if spec.startswith("table:") and "*" not in spec:
            ok = self.st.q("SELECT 1 FROM nodes WHERE id LIKE ? LIMIT 1", (f"column:{spec[6:]}.%",))
            return {"spec": spec, "ids": [spec] if ok else [], "status": "ok" if ok else "unresolved"}
        ids = Q.resolve_targets(self.st, spec)
        if "::" not in spec and not spec.startswith(("column:", "route:", "config:", "env:", "connection:", "page:")):
            cls = [i for i in ids if i.split(":", 1)[0] in ("class", "interface", "trait", "enum")]
            if cls:
                ids = cls
        if not ids:
            return {"spec": spec, "ids": [], "status": "unresolved"}
        if "*" not in spec and len(ids) > 1:
            return {"spec": spec, "ids": ids, "status": "ambiguous", "candidates": ids[:6]}
        return {"spec": spec, "ids": ids, "status": "ok"}

    def source_lines(self, file: str | None) -> list[str] | None:
        if not file:
            return None
        repo, _, rel = file.partition("/")
        cands = []
        if repo in self.src.roots:
            cands.append(self.src.roots[repo] / rel)
        cands += [r / file for r in self.src.roots.values()]
        for p in cands:
            if p.is_file():
                try:
                    return p.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    return None
        return None

    def span_text(self, nid) -> str | None:
        n = self.node(nid)
        if not n or not n.get("file") or not n.get("line"):
            return None
        lines = self.source_lines(n["file"])
        if lines is None:
            return None
        return "\n".join(lines[n["line"] - 1:(n.get("end_line") or n["line"])])

    def db_file(self, rel: str, repo: str | None) -> str:
        """Map a repo-relative path (snapshot evidence) to the DB's file column."""
        if self.combined and repo and not rel.startswith(repo + "/"):
            return f"{repo}/{rel}"
        return rel

    def enclosing(self, file: str, a: int, b: int) -> list[dict]:
        """Code nodes whose span overlaps [a, b]; else nodes declared on those lines."""
        rows = self.st.q("""SELECT id, kind, line, end_line FROM nodes WHERE file=? AND end_line IS NOT NULL
                            AND line <= ? AND end_line >= ? AND kind IN ('method','function')""", (file, b, a))
        if rows:
            return [dict(r) for r in rows]
        rows = self.st.q("SELECT id, kind, line, end_line FROM nodes WHERE file=? AND line BETWEEN ? AND ? AND kind != 'column'", (file, a, b))
        return [dict(r) for r in rows]

    def tables_of(self, ids) -> set[str]:
        out = set()
        for i in ids:
            for r in self.st.q("SELECT dst FROM edges WHERE src=? AND kind IN ('READS_TABLE','READS_COLUMN','WRITES_TABLE','WRITES_COLUMN','MENTIONS_COLUMN')", (i,)):
                out.add(r["dst"].split(":", 1)[1].split(".")[0])
        return out

    def closure_fwd(self, starts: list[str], kinds=("CALLS",), max_depth=6) -> set[str]:
        seen, frontier = set(starts), list(starts)
        kq = ",".join("?" * len(kinds))
        for _ in range(max_depth):
            nxt = []
            for x in frontier:
                for r in self.st.q(f"SELECT dst FROM edges WHERE src=? AND kind IN ({kq})", (x, *kinds)):
                    if r["dst"] not in seen:
                        seen.add(r["dst"]); nxt.append(r["dst"])
            frontier = nxt
        return seen

    def touchers(self, table: str, kinds) -> dict[str, list[dict]]:
        kq = ",".join("?" * len(kinds))
        rows = self.st.q(f"""SELECT src, kind, dst, file, line, confidence FROM edges WHERE kind IN ({kq})
                             AND (dst=? OR dst LIKE ?) ORDER BY src, line""", (*kinds, f"table:{table}", f"column:{table}.%"))
        out = defaultdict(list)
        for r in rows:
            if not r["src"].startswith("resolution:"):
                out[r["src"]].append(dict(r))
        return out

    def routes(self) -> list[dict]:
        out = []
        for r in self.st.q("SELECT id, file, line, attrs FROM nodes WHERE kind='route'"):
            a = json.loads(r["attrs"] or "{}")
            if not a.get("uri") or not a.get("method"):
                continue
            uris = [("as-declared", a["uri"])]
            f = (r["file"] or "").split("/", 1)[-1] if self.combined else (r["file"] or "")
            if f.startswith("routes/api"):
                uris.append(("api-prefixed", "/api" + a["uri"]))
            out.append({"id": r["id"], "uri": a["uri"], "method": a["method"], "uris": uris, "file": r["file"], "line": r["line"]})
        return out


def _item(check, node, why, evidence, severity="missing", chain=None, **kw):
    ev = list(dict.fromkeys(e for e in evidence if e and e != "?"))
    return {"check": check, "node": node, "why": why, "evidence": ev[:6], "severity": severity,
            **({"chain": chain} if chain else {}), **{k: v for k, v in kw.items() if v}}


def _hops(rows, t: str, limit=3) -> list[dict]:
    """Evidence edges of a toucher, folded onto the table node (columns are drawn as their table)."""
    out = []
    for r in rows[:limit]:
        out.append({"from": r["src"], "kind": r["kind"], "to": f"table:{t}", "at": loc(r["file"], r["line"]), "confidence": r["confidence"]})
    return out


def _ev(rows) -> list[str]:
    return [loc(r["file"], r["line"]) for r in rows if r.get("file")]


def is_covered(nid: str, cov: set[str]) -> bool:
    if nid in cov:
        return True
    if "::" in nid:
        owner = nid.split(":", 1)[1].split("::")[0]
        if f"class:{owner}" in cov or f"admin:{owner}" in cov:
            return True
    if nid.startswith("admin:") and "class:" + nid.split(":", 1)[1] in cov:
        return True
    return False


# ----------------------------------------------------------------------------------------------- resolution
def plan_refs(plan: dict) -> list[tuple[str, str]]:
    refs = []
    for i, it in enumerate(plan.get("modify") or []):
        refs.append((f"modify[{i}].target", it["target"]))
    for i, it in enumerate(plan.get("add_edges") or []):
        refs += [(f"add_edges[{i}].from", it["from"]), (f"add_edges[{i}].to", it["to"])]
    for i, it in enumerate(plan.get("forbid") or []):
        refs += [(f"forbid[{i}].from", it["from"]), (f"forbid[{i}].to", it["to"])]
        g = it.get("guard") or {}
        for k in ("at", "reads", "calls"):
            if g.get(k):
                refs.append((f"forbid[{i}].guard.{k}", g[k]))
    for i, it in enumerate(plan.get("require") or []):
        refs.append((f"require[{i}].route", it["route"]))
    for i, it in enumerate(plan.get("covers") or []):
        refs.append((f"covers[{i}].spec", it["spec"]))
    for i, it in enumerate(plan.get("out_of_scope") or []):
        refs.append((f"out_of_scope[{i}].spec", it["spec"]))
    for i, it in enumerate(plan.get("precedents") or []):
        for j, w in enumerate(it.get("within") or []):
            refs.append((f"precedents[{i}].within[{j}]", w))
    return [(w, str(s)) for w, s in refs]


def resolve_all(cx: Ctx) -> dict:
    out = [{"where": w, **cx.resolve(s)} for w, s in plan_refs(cx.plan)]
    planned = []
    new_tables = {it["id"][6:] for it in cx.plan.get("add_nodes") or [] if it["id"].startswith("table:")}
    for i, it in enumerate(cx.plan.get("add_nodes") or []):
        nid = it["id"]
        exists = bool(cx.node(nid))
        anchor = None
        if nid.startswith("column:"):
            t = nid[7:].split(".")[0]
            anchor = f"table:{t}"
            if t not in new_tables and cx.resolve(anchor)["status"] != "ok":
                out.append({"where": f"add_nodes[{i}].id (table)", "spec": anchor, "ids": [], "status": "unresolved"})
        elif "::" in nid:
            anchor = "class:" + nid.split(":", 1)[1].split("::")[0]
            if not cx.node(anchor) and anchor not in cx.planned_nodes:
                out.append({"where": f"add_nodes[{i}].id (owner class)", "spec": anchor, "ids": [], "status": "unresolved"})
        planned.append({"id": nid, "exists": exists, "anchor": anchor})
    return {"refs": out, "planned_nodes": planned}


def changed_tables(plan: dict) -> dict[str, list[str]]:
    """table -> planned new column names."""
    out: dict[str, list[str]] = {}
    for it in plan.get("add_nodes") or []:
        if it["id"].startswith("column:"):
            t, c = it["id"][7:].split(".", 1)
            out.setdefault(t, []).append(c)
        elif it["id"].startswith("table:"):
            out.setdefault(it["id"][6:], [])
    for it in plan.get("add_edges") or []:
        for end in (str(it["from"]), str(it["to"])):
            if end.startswith(("column:", "table:")) and it["kind"] in WRITE_KINDS + READ_KINDS:
                out.setdefault(end.split(":", 1)[1].split(".")[0], [])
    for it in plan.get("modify") or []:
        t = str(it["target"])
        if t.startswith(("table:", "column:")):
            out.setdefault(t.split(":", 1)[1].split(".")[0], [])
    return out


# ----------------------------------------------------------------------------------------------- completeness
class Collector:
    def __init__(self, cov: set[str]):
        self.cov, self.items, self.keys = cov, [], set()

    def add(self, it: dict):
        key = (it["check"], it["node"])
        if key in self.keys:
            return
        self.keys.add(key)
        it["covered"] = is_covered(it["node"], self.cov)
        self.items.append(it)

    @property
    def nodes(self) -> set[str]:
        return {i["node"] for i in self.items}


def _check_table(cx: Ctx, t: str, newcols: list[str], impacts: dict, through: dict, fwd: dict, col: Collector, res: dict):
    st = cx.st
    writers, readers = cx.touchers(t, WRITE_KINDS), cx.touchers(t, READ_KINDS)

    guards = [m for m in impacts if m in res.get("guards", [])]

    def bypassed(src):
        return [short(m) for m in guards if t in cx.tables_of(fwd[m]) and src not in through[m] and src not in fwd[m]]

    for src, rows in writers.items():
        cols = sorted({r["dst"].split(".", 1)[1] for r in rows if r["dst"].startswith("column:")})
        n = cx.node(src) or {}
        payload = len(cols) >= 2 or bool(PAYLOAD_VERBS.match(n.get("name") or ""))
        cs = (", ".join(cols[:5]) + ("…" if len(cols) > 5 else "")) if cols else "row"
        why = f"writes {t} ({cs})" + (f"; must set/keep new {', '.join(newcols)}" if payload and newcols else "")
        col.add(_item("table_writer", src, why, _ev(rows), "missing" if payload else "review", bypasses=bypassed(src), anchor=f"table:{t}", hops=_hops(rows, t)))
    for src, rows in readers.items():
        if src in writers:
            continue
        cols = sorted({r["dst"].split(".", 1)[1] for r in rows if r["dst"].startswith("column:")})
        col.add(_item("table_reader", src, f"reads {t} ({', '.join(cols[:5])}{'…' if len(cols) > 5 else ''})", _ev(rows), "review",
                      bypasses=bypassed(src), anchor=f"table:{t}", hops=_hops(rows, t)))
    models = [r["src"] for r in st.q("SELECT src FROM edges WHERE kind='MAPS_TO_TABLE' AND dst=?", (f"table:{t}",))]
    toucher_ids = set(writers) | set(readers)
    for mdl in models:
        mfqn = mdl.split(":", 1)[1]
        # Filament resources bound with $model = Model::class (form saves are not WRITES edges in the graph)
        for pr in st.q("SELECT id, file, line, attrs FROM nodes WHERE kind='property' AND name='$model'"):
            dflt = json.loads(pr["attrs"] or "{}").get("default")
            if not (isinstance(dflt, dict) and str(dflt.get("__class__", "")).lstrip("\\") == mfqn):
                continue
            rcls = pr["id"].split(":", 1)[1].split("::")[0]
            for meth in ("form", "table"):
                mid = f"method:{rcls}::{meth}"
                mn = cx.node(mid)
                if not mn:
                    continue
                if meth == "form":
                    why = f"Filament form for {short(mdl)} ($model @{bloc(loc(pr['file'], pr['line']))}); saves bypass the graph's WRITES edges" \
                          + (f" — must expose {', '.join(newcols)}" if newcols else "")
                else:
                    why = f"Filament table for {short(mdl)}" + (f" — show/filter {', '.join(newcols)}" if newcols else "")
                col.add(_item("admin_surface", mid, why, [loc(mn["file"], mn["line"])] + _ev(readers.get(mid, []))[:2],
                              "missing" if (meth == "form" or newcols) else "review", anchor=f"table:{t}"))
        fp = cx.node(f"property:{mfqn}::$fillable")
        if fp and newcols:
            fill = json.loads(fp["attrs"] or "{}").get("default") or []
            lacking = [c for c in newcols if isinstance(fill, list) and c not in fill]
            if lacking:
                col.add(_item("model_fillable", fp["id"], f"{short(mdl)}::$fillable lacks {', '.join(lacking)} (mass assignment would drop it)",
                              [loc(fp["file"], fp["line"])], "missing", anchor=f"table:{t}"))
        # API resources: JsonResource subclasses instantiated by code touching the table, or named <Model>Resource
        mshort = mfqn.rsplit("\\", 1)[-1]
        rcs: dict[str, list[str]] = {}
        for r in st.q("""SELECT e.src, e.dst, e.file, e.line FROM edges e JOIN edges x ON x.src = e.dst
                         WHERE e.kind='INSTANTIATES' AND x.kind='EXTENDS' AND x.dst LIKE '%JsonResource'"""):
            if r["src"] in toucher_ids:
                rcs.setdefault(r["dst"], []).append(loc(r["file"], r["line"]))
        for r in st.q("SELECT id FROM nodes WHERE kind='class' AND name=?", (mshort + "Resource",)):
            if st.q("SELECT 1 FROM edges WHERE src=? AND kind='EXTENDS' AND dst LIKE '%JsonResource'", (r["id"],)):
                rcs.setdefault(r["id"], [])
        for rc, ev in rcs.items():
            ta = f"method:{rc.split(':', 1)[1]}::toArray"
            tn = cx.node(ta)
            if tn:
                col.add(_item("api_resource", ta, f"serializes {short(mdl)} in API responses"
                              + (f" (built at {', '.join(bloc(e) for e in ev[:3])})" if ev else "")
                              + (f"; decide whether to expose {', '.join(newcols)}" if newcols else ""),
                              [loc(tn["file"], tn["line"])] + ev, "missing" if newcols else "review", anchor=f"table:{t}"))
        for r in st.q("SELECT src, file, line FROM edges WHERE kind='REFERENCES' AND dst=? AND src LIKE 'method:%'", (mdl,)):
            if st.q("SELECT 1 FROM edges WHERE kind='HAS_RELATION' AND dst=? AND line=?", (mdl, r["line"])):
                col.add(_item("relation", r["src"], f"Eloquent relation to {short(mdl)}", [loc(r["file"], r["line"])], "review", anchor=f"table:{t}"))
    # form requests validating this table's payload but not the new column(s)
    colnames = {r["id"].split(".", 1)[1] for r in st.q("SELECT id FROM nodes WHERE id LIKE ?", (f"column:{t}.%",))}
    val = defaultdict(list)
    for r in st.q("SELECT src, dst, file, line FROM edges WHERE kind='VALIDATES'"):
        val[r["src"]].append(dict(r))
    for src, rows in val.items():
        keys = {r["dst"].split(":", 1)[1] for r in rows}
        ov = keys & colnames
        if len(ov) >= max(3, 0.5 * len(colnames - {"id", "created_at", "updated_at"})) and len(ov) >= 0.5 * len(keys):
            lacking = [c for c in newcols if c not in keys]
            if lacking:
                col.add(_item("validation", src, f"validates {len(ov)} {t} fields ({', '.join(sorted(ov)[:4])}…) but not {', '.join(lacking)}",
                              _ev(rows)[:2], "missing", anchor=f"table:{t}"))
    # identity columns on other tables (e.g. orders.book_id / orders.book_isbn) and their consumers
    sg = singular(t)
    idrx = re.compile(rf"^(\w+_)?{re.escape(sg)}_(id|code)$")
    refcols = [r["id"] for r in st.q("SELECT id FROM nodes WHERE kind='column' AND id NOT LIKE ?", (f"column:{t}.%",))
               if idrx.match(r["id"].split(".", 1)[1]) and r["id"][7:].split(".")[0] != sg]
    res.setdefault("referencing_columns", {})[t] = refcols
    for c in refcols:
        for r in st.q("SELECT src, kind, file, line FROM edges WHERE dst=? AND kind IN ('READS_COLUMN','MENTIONS_COLUMN','WRITES_COLUMN')", (c,)):
            if not r["src"].startswith("resolution:"):
                col.add(_item("referencing_column", r["src"], f"{r['kind'].lower().replace('_', ' ')} {c[7:]} (refers to {t})",
                              [loc(r["file"], r["line"])], "review", anchor=c))
    rel = [r["src"].split("::")[-1] for m in models for r in st.q("SELECT src FROM edges WHERE kind='REFERENCES' AND dst=? AND src LIKE 'method:%'", (m,))
           if st.q("SELECT 1 FROM edges WHERE kind='HAS_RELATION' AND dst=?", (m,))]
    names = sorted({c.split(".", 1)[1] for c in refcols} | set(rel))
    if names:
        for hit in _text_mentions(cx, names, col):
            hit["anchor"] = f"table:{t}"
            col.add(hit)


def _text_mentions(cx: Ctx, names: list[str], col: Collector, cap=40) -> list[dict]:
    """Exact-regex mentions of identity columns / relation names in indexed PHP + Blade files where the graph has no
    column edge on that line (Blade templates, JsonResource `$this->x`). Labelled text-match; never becomes an edge."""
    cols = [n for n in names if "_" in n]
    rels = [n for n in names if "_" not in n]
    alts = []
    if cols:
        alts.append(r"(?:->|['\"])(" + "|".join(map(re.escape, cols)) + r")\b")
    if rels:
        alts.append(r"->(" + "|".join(map(re.escape, rels)) + r")\b(?!\s*\()")
    rx = re.compile("|".join(alts))
    out, already = [], col.nodes
    model_files = {(cx.node(r["src"]) or {}).get("file") for r in cx.st.q("SELECT src FROM edges WHERE kind='MAPS_TO_TABLE'")}
    for repo, root in sorted(cx.src.roots.items()):
        for sub in mention_dirs(cx.st, repo):
            base = root / sub if sub else root
            if not base.is_dir():
                continue
            for p in sorted(base.rglob("*.php")):
                rel = str(p.relative_to(root))
                dbf = f"{repo}/{rel}" if cx.combined and repo else rel
                if dbf in model_files:
                    continue  # model declarations ($fillable/$casts) of the table itself are not consumers
                try:
                    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    continue
                hits = [i + 1 for i, ln in enumerate(lines) if rx.search(ln)]
                by_node = defaultdict(list)
                for h in hits:
                    enc = cx.enclosing(dbf, h, h)
                    nid = min(enc, key=lambda e: (e.get("end_line") or e["line"]) - e["line"])["id"] if enc else f"file:{dbf}"
                    if nid in already or is_covered(nid, col.cov):
                        continue
                    if cx.st.q("SELECT 1 FROM edges WHERE file=? AND line=? AND kind IN ('READS_COLUMN','MENTIONS_COLUMN','WRITES_COLUMN')", (dbf, h)):
                        continue
                    by_node[nid].append(h)
                for nid, hs in by_node.items():
                    found = sorted({g for h in hs for m in rx.finditer(lines[h - 1]) for g in m.groups() if g})
                    out.append(_item("text_mention", nid, f"mentions {', '.join(found)} (text-match, no graph edge)",
                                     [loc(dbf, h) for h in hs], "review"))
                    if len(out) >= cap:
                        return out
    return out


def _check_modified(cx: Ctx, mm: list[str], ct: dict, impacts: dict, through: dict, fwd: dict, col: Collector, res: dict):
    st = cx.st
    route_mw, per_m = {}, defaultdict(dict)
    for m, imp in impacts.items():
        for e in imp["entry_points"]:
            n = cx.node(e["id"]) or {}
            if e["kind"] == "route":
                route_mw[e["id"]] = per_m[m][e["id"]] = json.loads(n.get("attrs") or "{}").get("middleware") or []
            col.add(_item("entry_point", e["id"], f"{e['entry_kind']} reaching modified {short(m)}", [loc(n.get("file"), n.get("line"))],
                          "missing" if e["kind"] == "route" else "review", chain=fmt_chain(e["path"]), anchor=m, hops=e["path"]))
        own = m.split(":", 1)[1].split("::")[0]
        for c in [r["dst"] for r in st.q("SELECT DISTINCT dst FROM edges WHERE src=? AND kind='CALLS'", (m,))]:
            if not (c.split(":", 1)[1].startswith(own + "::") or cx.tables_of({c}) & set(ct)):
                continue
            for r in st.q("SELECT DISTINCT src, file, line FROM edges WHERE dst=? AND kind='CALLS' AND src != ?", (c, m)):
                if r["src"] in through[m] or r["src"] in fwd[m]:
                    continue
                col.add(_item("bypass_caller", r["src"], f"calls {short(c)} directly, not through modified {short(m)}",
                              [loc(r["file"], r["line"])], "review", anchor=c,
                              hops=[{"from": r["src"], "kind": "CALLS", "to": c, "at": loc(r["file"], r["line"]), "confidence": "resolved"}]))
        # direct callers of a modified method must adapt to it (signature / new rejection)
        for r in st.q("SELECT DISTINCT src, file, line FROM edges WHERE dst=? AND kind='CALLS'", (m,)):
            if r["src"].split(":", 1)[0] in CODE_KINDS:
                col.add(_item("caller", r["src"], f"calls modified {short(m)}", [loc(r["file"], r["line"])], "missing", anchor=m,
                              hops=[{"from": r["src"], "kind": "CALLS", "to": m, "at": loc(r["file"], r["line"]), "confidence": "resolved"}]))
    res["entry_routes"] = route_mw
    diff = {}
    for m, rmw in per_m.items():  # routes reaching the SAME modified method should agree on auth middleware
        if len(rmw) > 1:
            allmw = set().union(*map(set, rmw.values()))
            for rid, mw in rmw.items():
                if allmw - set(mw):
                    diff.setdefault(rid, {"lacks": sorted(allmw - set(mw)), "peers": sorted(x for x in rmw if x != rid), "via": short(m)})
    res["middleware_diff"] = diff
    # same-class siblings of modified methods sharing the verb (reserveLocal ~ reserveFromWarehouse)
    for m in impacts:
        n = cx.node(m) or {}
        verb = re.match(r"[a-z]+", n.get("name") or "")
        if not verb or len(verb.group(0)) < 3:
            continue
        own = m.split(":", 1)[1].split("::")[0]
        for r in st.q("SELECT id, name, file, line FROM nodes WHERE kind='method' AND fqn LIKE ? AND id != ?", (own + "::%", m)):
            if r["name"].startswith(verb.group(0)) and r["name"] != n["name"] and r["id"] not in mm:
                col.add(_item("parallel_method", r["id"], f"same-class sibling of modified {short(m)} ({verb.group(0)}*)",
                              [loc(r["file"], r["line"])], "review", anchor=m))


def _check_clients(cx: Ctx, refs: list[dict], col: Collector, res: dict):
    st = cx.st
    affected = set(res.get("entry_routes") or {}) | {i for r in refs if r["where"].startswith("require") for i in r["ids"]}
    res["affected_routes"] = sorted(affected)
    for rid in sorted(affected):
        for m in st.q("SELECT src FROM edges WHERE kind='MATCHES_ROUTE' AND dst=?", (rid,)):
            for c in st.q("SELECT src, file, line FROM edges WHERE kind='HTTP_CALLS' AND dst=?", (m["src"],)):
                pages = Q.reverse_closure(st, [c["src"]], kinds=Q.CALL_LIKE)
                for p in sorted(x for x in pages if x.startswith("page:"))[:6] or [c["src"]]:
                    col.add(_item("frontend_caller", p, f"calls {rid.split(':', 1)[1]} via {short(c['src'])}", [loc(c["file"], c["line"])], "missing", anchor=rid))
    from .link import match_endpoint
    routes = None
    for snap in cx.plan.get("_clients") or []:
        base = snap.get("base_prefix") or ""
        for c in snap.get("calls") or []:
            if not c.get("path"):
                continue
            routes = routes or cx.routes()
            mres = match_endpoint(c.get("method") or "GET", base + c["path"], routes)
            hit = [m["route"] for m in mres["matched"] if m["route"] in affected]
            if hit:
                ev = f"{c['repo']}@{str(c.get('commit'))[:8]}:{c['file']}" + (f":{c['line']}" if c.get("line") else "")
                col.add(_item("external_client", f"client:{c['repo']}/{c['file']}",
                              f"{c.get('method')} {c['path']} -> {hit[0].split(':', 1)[1]} [snapshot, {c.get('evidence', '?')}]"
                              + (f"; sends {', '.join(c.get('sends') or [])}" if c.get("sends") else ""), [ev], "missing", anchor=hit[0]))


def snapshot_clients(st, route_ids, root: str | Path | None = None) -> list[dict]:
    """External client call sites (read-only snapshot files next to the plans: `snapshot_version` + `calls`) that match
    any of route_ids. Used by impact, so callers in apps that are not indexed show up without a plan."""
    from .link import match_endpoint
    want = set(route_ids)
    if not want:
        return []
    d = plans_dir(root)
    if not d.is_dir():
        return []
    combined = bool(st.meta().get("repos"))
    routes = None
    out = []
    for f in sorted(d.glob("*.y*ml")):
        try:
            snap = _yaml(f)
        except Exception:  # noqa: BLE001
            continue
        if "snapshot_version" not in snap or not isinstance(snap.get("calls"), list):
            continue
        base = snap.get("base_prefix") or ""
        for c in snap["calls"]:
            if not isinstance(c, dict) or not c.get("path"):
                continue
            if routes is None:
                routes = []
                for r in st.q("SELECT id, file, line, attrs FROM nodes WHERE kind='route'"):
                    a = json.loads(r["attrs"] or "{}")
                    if not a.get("uri") or not a.get("method"):
                        continue
                    uris = [("as-declared", a["uri"])]
                    rf = (r["file"] or "").split("/", 1)[-1] if combined else (r["file"] or "")
                    if rf.startswith("routes/api"):
                        uris.append(("api-prefixed", "/api" + a["uri"]))
                    routes.append({"id": r["id"], "uri": a["uri"], "method": a["method"], "uris": uris,
                                   "file": r["file"], "line": r["line"]})
            hit = [m["route"] for m in match_endpoint(c.get("method") or "GET", base + c["path"], routes)["matched"]
                   if m["route"] in want]
            if hit:
                out.append({"route": hit[0], "repo": c.get("repo"), "commit": str(c.get("commit") or "")[:8],
                            "file": c.get("file"), "line": c.get("line"), "method": c.get("method"), "path": c["path"],
                            "sends": c.get("sends") or [], "evidence": c.get("evidence", "?"), "snapshot": f.name})
    return out


def _check_parallel(cx: Ctx, ct: dict, impacts: dict, fwd: dict, col: Collector):
    st = cx.st
    allcols = defaultdict(set)
    for r in st.q("SELECT id FROM nodes WHERE kind='column'"):
        tt, cc = r["id"][7:].split(".", 1)
        allcols[tt].add(cc)
    boring = {"id", "created_at", "updated_at"}
    reached_any = set().union(*fwd.values()) if fwd else set()
    for t, newc in ct.items():
        mine = allcols.get(t, set()) - boring
        if len(mine) < 3:
            continue
        mine_models = [r["src"] for r in st.q("SELECT src FROM edges WHERE kind='MAPS_TO_TABLE' AND dst=?", (f"table:{t}",))]
        for tt, cols in sorted(allcols.items()):
            cols = cols - boring
            if tt == t or not cols:
                continue
            tm = [r["src"] for r in st.q("SELECT src FROM edges WHERE kind='MAPS_TO_TABLE' AND dst=?", (f"table:{tt}",))]
            if not tm:
                continue  # generic tables (no model) are not mirrors
            j = len(mine & cols) / len(mine | cols)
            if j < 0.5:
                continue
            reach = [short(m) for m in impacts if tt in cx.tables_of(fwd[m])]
            ev = [loc((cx.node(x) or {}).get("file"), (cx.node(x) or {}).get("line")) for x in tm]
            col.add(_item("parallel_table", f"table:{tt}",
                          f"mirrors {t} ({len(mine & cols)}/{len(mine | cols)} columns; model {', '.join(short(x) for x in tm)})"
                          + (f"; has no {', '.join(c for c in newc if c not in cols)}" if newc else "")
                          + (f"; reached from modified {', '.join(reach)}" if reach else ""), ev, "review", anchor=f"table:{t}"))
            for a in tm:
                pa = {r["name"]: r["id"] for r in st.q("SELECT id, name FROM nodes WHERE kind='method' AND fqn LIKE ?", (a.split(":", 1)[1] + "::%",))}
                for b in mine_models:
                    pb = {r["name"]: r["id"] for r in st.q("SELECT id, name FROM nodes WHERE kind='method' AND fqn LIKE ?", (b.split(":", 1)[1] + "::%",))}
                    for name in sorted(set(pa) & set(pb)):
                        if pa[name] in reached_any:
                            n = cx.node(pa[name]) or {}
                            col.add(_item("parallel_method", pa[name], f"mirror of {short(pb[name])}; reached from modified code",
                                          [loc(n.get("file"), n.get("line"))], "review", anchor=pb[name]))


def _precedents(cx: Ctx, mm: list[str], impacts: dict) -> list[dict]:
    out = []
    for p in cx.plan.get("precedents") or []:
        rx = re.compile(p["pattern"])
        scope = [i for w in p.get("within") or [] for i in cx.resolve(w)["ids"]]
        if not scope:
            scope = list(mm) + [c["id"] for imp in impacts.values() for c in imp["callers"] if c["depth"] == 1]
        hits = []
        for nid in dict.fromkeys(scope):
            n, txt = cx.node(nid), cx.span_text(nid)
            if not n or not txt:
                continue
            for k, ln in enumerate(txt.splitlines()):
                if rx.search(ln):
                    hits.append({"node": nid, "at": loc(n["file"], n["line"] + k), "text": ln.strip()[:120]})
        out.append({"name": p["name"], "pattern": p["pattern"], "for": p.get("for"), "hits": hits})
    return out


# ----------------------------------------------------------------------------------------------- conflicts
def _path_to_any(st: GraphStore, src: str, dsts: set[str], max_depth=12) -> list[dict]:
    kset, prev, seen, frontier = set(PROPAGATING), {}, {src}, [src]
    for _ in range(max_depth):
        nxt = []
        for x in frontier:
            for e in st.q("SELECT src,dst,kind,file,line,confidence,gate FROM edges WHERE src=?", (x,)):
                if e["kind"] not in kset or e["dst"] in seen:
                    continue
                seen.add(e["dst"]); prev[e["dst"]] = dict(e); nxt.append(e["dst"])
                if e["dst"] in dsts:
                    path, y = [], e["dst"]
                    while y in prev:
                        p = prev[y]
                        path.append({"from": p["src"], "kind": p["kind"], "to": p["dst"], "at": loc(p["file"], p["line"]),
                                     "confidence": p["confidence"], **({"gated": p["gate"]} if p["gate"] else {})})
                        y = p["src"]
                    return path[::-1]
        frontier = nxt
    return []


def forbid_status(cx: Ctx, f: dict) -> dict:
    st = cx.st
    srcs = cx.resolve(f["from"])["ids"]
    to = str(f["to"])
    dsts = list(cx.resolve(to)["ids"])
    if to.startswith("table:"):
        dsts += [r["id"] for r in st.q("SELECT id FROM nodes WHERE id LIKE ?", (f"column:{to[6:]}.%",))]
    out = {"id": f["id"], "type": f.get("type", "path"), "from": f["from"], "to": to, "when": f.get("when"),
           "issues": f.get("issues") or [], "present": False}
    if f.get("type") == "edge":
        for s in srcs:
            for d in dsts:
                r = st.q("SELECT file, line FROM edges WHERE src=? AND dst=? AND kind=?", (s, d, f["edge_kind"]))
                if r and not out["present"]:
                    out.update(present=True, chain=f"{short(s)} -{f['edge_kind']}@{bloc(loc(r[0]['file'], r[0]['line']))}-> {short(d)}",
                               hops=[{"from": s, "to": d, "kind": f["edge_kind"], "at": loc(r[0]["file"], r[0]["line"]), "confidence": "exact"}])
    else:
        for s in srcs:
            p = _path_to_any(st, s, set(dsts))
            if p:
                out.update(present=True, chain=fmt_chain(p), hops=p)
                break
    g = f.get("guard")
    if g and out["present"]:
        scope = cx.closure_fwd(cx.resolve(g["at"])["ids"], max_depth=1)
        kinds, tgt = (("READS_COLUMN", "MENTIONS_COLUMN"), g["reads"]) if g.get("reads") else (("CALLS",), g["calls"])
        ev = []
        for s in scope:
            for t in cx.resolve(tgt)["ids"]:
                ev += [loc(r["file"], r["line"]) for r in st.q(f"SELECT file, line FROM edges WHERE src=? AND dst=? AND kind IN ({','.join('?' * len(kinds))})",
                                                                (s, t, *kinds))]
        out["guard"] = {"at": g["at"], "needs": f"{'reads' if g.get('reads') else 'calls'} {tgt}", "present": bool(ev), "evidence": ev[:4]}
    return out


def linked_issue_ids(plan: dict) -> set[str]:
    refs = list(plan.get("issues") or [])
    for sec in ("add_nodes", "modify", "add_edges", "forbid", "require"):
        for it in plan.get(sec) or []:
            refs += it.get("issues") or []
    out = set()
    for r in refs:
        m = re.search(r"#(\d+)\s*$", r) or re.search(r"/issues/(\d+)", r)
        if m:
            out.add(f"#{m.group(1)}")
    return out


def finding_overlap(cx: Ctx, touched: set[str]) -> list[dict]:
    snap = cx.plan.get("_findings")
    if not snap:
        return []
    linked = linked_issue_ids(cx.plan)
    repo = snap.get("graph_repo")
    out = []
    for f in snap.get("findings") or []:
        hits = defaultdict(list)
        for file, a, b in f["_evidence"]:
            for n in cx.enclosing(cx.db_file(file, repo), a, b):
                if is_covered(n["id"], touched):
                    hits[n["id"]].append(f"L{a}" + (f"-{b}" if b != a else ""))
        out.append({"id": f["id"], "title": f.get("title"), "state": f.get("state", "open"), "url": f.get("url"),
                    "linked": f["id"] in linked, "touches": dict(sorted(hits.items()))})
    return out


# ----------------------------------------------------------------------------------------------- the check
def check(st: GraphStore, plan: dict, verify: bool = False, baseline: dict | None = None,
          roots: dict[str, str] | None = None, min_conf: str = "heuristic") -> dict:
    cx = Ctx(st, plan, roots)
    m = st.meta()
    res = {"plan": plan.get("name"), "title": plan.get("title"), "status": plan.get("status", "draft"), "file": plan.get("_file"),
           "mode": "verify" if verify else "plan", "graph": {"indexed_at": m.get("indexed_at"), "project": m.get("project")},
           "schema_errors": plan.get("_schema_errors") or []}
    rs = resolve_all(cx)
    res["resolve"] = rs
    refs = rs["refs"]
    cov = {i for r in refs for i in r["ids"]}
    mm = list(dict.fromkeys(i for it in plan.get("modify") or [] for i in cx.resolve(it["target"])["ids"]))
    res["modified"] = mm
    ct = changed_tables(plan)
    res["changed_tables"] = ct
    res["guards"] = [i for it in plan.get("modify") or [] if it.get("role") == "guard" for i in cx.resolve(it["target"])["ids"]]
    col = Collector(cov)
    code_mm = [x for x in mm if x.split(":", 1)[0] in CODE_KINDS and cx.node(x)]
    impacts = {x: Q.impact(st, x, min_conf=min_conf) for x in code_mm}
    through = {x: {x} | {c["id"] for c in imp["callers"]} | {e["id"] for e in imp["entry_points"]} for x, imp in impacts.items()}
    fwd = {x: cx.closure_fwd([x]) for x in impacts}
    for t, newcols in ct.items():
        _check_table(cx, t, newcols, impacts, through, fwd, col, res)
    _check_modified(cx, mm, ct, impacts, through, fwd, col, res)
    _check_clients(cx, refs, col, res)
    _check_parallel(cx, ct, impacts, fwd, col)
    res["precedents"] = _precedents(cx, mm, impacts)
    res["conflicts"] = [forbid_status(cx, f) for f in plan.get("forbid") or []]
    req = []
    for r in plan.get("require") or []:
        for rid in cx.resolve(r["route"])["ids"]:
            n = cx.node(rid) or {}
            mw = json.loads(n.get("attrs") or "{}").get("middleware") or []
            need = r.get("middleware") or []
            req.append({"route": rid, "middleware": mw, "required": need, "missing": [x for x in need if x not in mw],
                        "at": loc(n.get("file"), n.get("line"))})
    res["require"] = req
    res["findings"] = finding_overlap(cx, cov | set(mm) | col.nodes)
    for f in res["findings"]:
        f["touches_plan"] = sorted(k for k in f["touches"] if is_covered(k, cov | set(mm)))
    if verify:
        res["verify"] = verify_impl(cx, baseline)
    order = {"missing": 0, "review": 1}
    res["items"] = sorted(col.items, key=lambda x: (order[x["severity"]], x["check"], x["node"]))
    unresolved = [r for r in refs if r["status"] in ("unresolved", "ambiguous")]
    items = res["items"]
    res["summary"] = {
        "references": len(refs), "unresolved": len(unresolved),
        "missing_from_plan": sum(1 for i in items if i["severity"] == "missing" and not i["covered"]),
        "review": sum(1 for i in items if i["severity"] == "review" and not i["covered"]),
        "covered": sum(1 for i in items if i["covered"]),
        "forbidden_paths_present": sum(1 for c in res["conflicts"] if c["present"]),
        "open_findings_touching": sum(1 for f in res["findings"] if f["touches"] and f["state"] == "open"),
        "unlinked_open_findings": sum(1 for f in res["findings"] if f["touches_plan"] and not f["linked"] and f["state"] == "open"),
        "requirements_failed": sum(1 for r in req if r["missing"]),
    }
    if verify:
        v = res["verify"]
        res["summary"]["verify"] = {k: f"{sum(1 for x in v[k] if x['ok'])}/{len(v[k])}" for k in ("nodes", "edges", "modified", "forbid", "require")}
        res["summary"]["verify_ok"] = all(x["ok"] for k in ("nodes", "edges", "forbid", "require") for x in v[k])
    s = res["summary"]
    res["ok"] = (not res["schema_errors"] and not unresolved and s["missing_from_plan"] == 0 and s["requirements_failed"] == 0
                 and (s.get("verify_ok", True) if verify else True))
    return res


# ----------------------------------------------------------------------------------------------- verify / baseline
def _sha(txt: str | None) -> str | None:
    return hashlib.sha1(txt.encode()).hexdigest()[:12] if txt is not None else None


def baseline_path(plan: dict) -> Path:
    return Path(plan["_file"]).with_suffix(".baseline.json")


def load_baseline(plan: dict) -> dict | None:
    p = baseline_path(plan)
    return json.loads(p.read_text()) if p.is_file() else None


def make_baseline(st: GraphStore, plan: dict, roots: dict[str, str] | None = None) -> dict:
    """Fingerprint the source span of every modified target (sha1 of its lines) so verify mode can tell whether the
    implementation touched it. Written next to the plan as <name>.baseline.json."""
    cx = Ctx(st, plan, roots)
    tg = {}
    for it in plan.get("modify") or []:
        for nid in cx.resolve(it["target"])["ids"]:
            n = cx.node(nid) or {}
            tg[nid] = {"file": n.get("file"), "line": n.get("line"), "end_line": n.get("end_line"), "sha1": _sha(cx.span_text(nid))}
    return {"plan": plan.get("name"), "graph_indexed_at": st.meta().get("indexed_at"), "targets": tg,
            "edges_present": {f"{it['from']} {it['kind']} {it['to']}": bool(edge_evidence(cx, it)) for it in plan.get("add_edges") or []}}


def edge_evidence(cx: Ctx, it: dict) -> list[str]:
    """Real-graph evidence for a planned edge (READS_COLUMN also accepts MENTIONS_COLUMN; noted)."""
    srcs, dsts = cx.resolve(it["from"])["ids"], cx.resolve(it["to"])["ids"]
    kinds = (it["kind"], "MENTIONS_COLUMN") if it["kind"] == "READS_COLUMN" else (it["kind"],)
    def find(ss, via=None):
        ev = []
        for s in ss:
            for d in dsts:
                for r in cx.st.q(f"SELECT file, line, confidence, kind FROM edges WHERE src=? AND dst=? AND kind IN ({','.join('?' * len(kinds))})",
                                 (s, d, *kinds)):
                    ev.append(f"{bloc(loc(r['file'], r['line']))} [{r['confidence']}{'' if r['kind'] == it['kind'] else ', as ' + r['kind']}"
                              f"{', via ' + short(s) if via else ''}]")
        return ev
    ev = find(srcs)
    if not ev:  # the planned edge may be implemented in a same-class helper the method calls (reserve -> reserveLocal)
        helpers = []
        for s in srcs:
            if "::" in s:
                own = s.split(":", 1)[1].split("::")[0] + "::"
                helpers += [h for h in cx.closure_fwd([s], max_depth=2) if h != s and h.split(":", 1)[1].startswith(own)]
        ev = find(sorted(set(helpers)), via=True)
    return ev


def verify_impl(cx: Ctx, base: dict | None) -> dict:
    plan = cx.plan
    v = {"nodes": [], "edges": [], "modified": [], "forbid": [], "require": []}
    for it in plan.get("add_nodes") or []:
        n = cx.node(it["id"])
        v["nodes"].append({"id": it["id"], "ok": bool(n), "at": loc(n["file"], n["line"]) if n else None})
    for it in plan.get("add_edges") or []:
        ev = edge_evidence(cx, it)
        v["edges"].append({"edge": f"{short(it['from'])} -{it['kind']}-> {short(it['to'])}", "ok": bool(ev), "evidence": ev[:3]})
    for it in plan.get("modify") or []:
        for nid in cx.resolve(it["target"])["ids"]:
            now = _sha(cx.span_text(nid))
            was = ((base or {}).get("targets") or {}).get(nid, {}).get("sha1")
            state = "no baseline" if was is None else ("missing now" if now is None else ("changed" if now != was else "UNCHANGED"))
            v["modified"].append({"target": nid, "intent": it["intent"], "ok": state == "changed", "state": state})
    for f in plan.get("forbid") or []:
        s = forbid_status(cx, f)
        ok = (not s["present"]) or bool((s.get("guard") or {}).get("present"))
        v["forbid"].append({"id": f["id"], "ok": ok, "present": s["present"], "guard": s.get("guard"), "chain": s.get("chain")})
    for r in plan.get("require") or []:
        for rid in cx.resolve(r["route"])["ids"]:
            mw = json.loads((cx.node(rid) or {}).get("attrs") or "{}").get("middleware") or []
            miss = [x for x in r.get("middleware") or [] if x not in mw]
            v["require"].append({"route": rid, "ok": not miss, "missing": miss})
    return v


# ----------------------------------------------------------------------------------------------- rendering
def render_load(plan: dict) -> str:
    o = [f"PLAN {plan.get('name')} v{plan.get('plan_version')} [{plan.get('status', 'draft')}] {plan.get('title')}",
         f"file: {plan.get('_file')}"]
    if plan.get("issues"):
        o.append(f"issues: {', '.join(plan['issues'])}")
    if plan.get("rationale"):
        o.append("rationale: " + " ".join(str(plan["rationale"]).split())[:500])
    o += [f"assume: {a}" for a in plan.get("assumptions") or []]
    for it in plan.get("add_nodes") or []:
        o.append(f"  + node {it['id']}" + (f"  {json.dumps(it.get('attrs'))}" if it.get("attrs") else ""))
    for it in plan.get("modify") or []:
        o.append(f"  ~ {it['target']}: {it['intent']}" + (f"  ({', '.join(it['issues'])})" if it.get("issues") else ""))
    for it in plan.get("add_edges") or []:
        o.append(f"  + edge {it['from']} -{it['kind']}-> {it['to']}" + (f"  ({it['intent']})" if it.get("intent") else ""))
    for it in plan.get("forbid") or []:
        o.append(f"  x forbid {it['id']}: {it.get('type', 'path')} {it['from']} => {it['to']}" + (f" when {it['when']}" if it.get("when") else ""))
    for it in plan.get("require") or []:
        o.append(f"  ! require {it['route']} middleware {', '.join(it.get('middleware') or [])}")
    for it in plan.get("covers") or []:
        o.append(f"  = covers {it['spec']}" + (f" ({it['note']})" if it.get("note") else ""))
    for it in plan.get("out_of_scope") or []:
        o.append(f"  - out of scope {it['spec']}: {it['reason']}")
    for it in plan.get("precedents") or []:
        o.append(f"  ? precedent {it['name']}: /{it['pattern']}/")
    ctx = plan.get("context") or {}
    if ctx:
        o.append(f"context: findings={ctx.get('findings')} clients={ctx.get('clients')}")
    if plan.get("_schema_errors"):
        o.append("SCHEMA ERRORS:")
        o += [f"  {e}" for e in plan["_schema_errors"]]
    return "\n".join(o)


def validate(st: GraphStore, plan: dict, roots=None) -> dict:
    cx = Ctx(st, plan, roots)
    rs = resolve_all(cx)
    bad = [r for r in rs["refs"] if r["status"] in ("unresolved", "ambiguous")]
    return {"plan": plan.get("name"), "schema_errors": plan.get("_schema_errors") or [], "refs": rs["refs"], "planned_nodes": rs["planned_nodes"],
            "unresolved": bad, "ok": not bad and not plan.get("_schema_errors")}


def render_validate(v: dict) -> str:
    n = len(v["refs"])
    o = [f"plan {v['plan']}: schema {'OK' if not v['schema_errors'] else 'ERRORS'}; references {n - len(v['unresolved'])}/{n} resolve"
         + ("" if v["ok"] else "  -> INVALID")]
    o += [f"  schema: {e}" for e in v["schema_errors"]]
    for r in v["unresolved"]:
        o.append(f"  {r['status'].upper()} {r['where']}: {r['spec']}" + (f"  candidates: {', '.join(r['candidates'])}" if r.get("candidates") else ""))
    for p in v["planned_nodes"]:
        o.append(f"  planned {p['id']}: {'ALREADY EXISTS in graph' if p['exists'] else 'new (not in graph yet)'}")
    return "\n".join(o)


def _fmt_item(i: dict) -> str:
    b = f" [bypasses {', '.join(i['bypasses'])}]" if i.get("bypasses") else ""
    s = f"  - [{i['check']}] {short(i['node'])}: {i['why']}{b}\n      @ {', '.join(i['evidence'][:3]) or '-'}"
    if i.get("chain") and i["check"] != "entry_point":
        s += f"\n      chain: {i['chain']}"
    return s


def render_check(res: dict, max_items: int = 60, show_covered: bool = True) -> str:
    s = res["summary"]
    o = [f"PLAN CHECK {res['plan']} [{res['mode']} mode] {res['title']}",
         f"graph {res['graph'].get('project')} indexed {res['graph'].get('indexed_at')}; plan {res['file']}",
         f"summary: refs {s['references'] - s['unresolved']}/{s['references']} resolve | MISSING FROM PLAN {s['missing_from_plan']} | "
         f"review {s['review']} | covered {s['covered']} | forbidden paths present {s['forbidden_paths_present']} | "
         f"open findings touching {s['open_findings_touching']} (unlinked {s['unlinked_open_findings']}) | requirements failed {s['requirements_failed']}"
         + (f" | verify {s['verify']}" if s.get("verify") else "")]
    o += [f"SCHEMA {e}" for e in res["schema_errors"]]
    bad = [r for r in res["resolve"]["refs"] if r["status"] in ("unresolved", "ambiguous")]
    o += ["", "1 RESOLVE: " + ("all plan references resolve to graph nodes" if not bad else f"{len(bad)} problem(s)")]
    for r in bad:
        o.append(f"  {r['status'].upper()} {r['where']}: {r['spec']}" + (f" -> {', '.join(r['candidates'])}" if r.get("candidates") else ""))
    for p in res["resolve"]["planned_nodes"]:
        o.append(f"  planned {p['id']}: {'EXISTS in graph' if p['exists'] else 'new'}")
    o.append("  changed tables: " + ", ".join(f"{t} (+{', '.join(c) or 'no new columns'})" for t, c in res["changed_tables"].items()))
    o.append("  modified: " + ", ".join(short(x) for x in res["modified"]))
    for r in res.get("require") or []:
        o.append(f"  require {r['route'].split(':', 1)[1]} [{', '.join(r['required'])}]: {'OK' if not r['missing'] else 'MISSING ' + ', '.join(r['missing'])}"
                 f" (has {', '.join(r['middleware'])} @{bloc(r['at'])})")
    for rid, d in (res.get("middleware_diff") or {}).items():
        o.append(f"  middleware: {rid.split(':', 1)[1]} lacks {', '.join(d['lacks'])} which peer route(s) "
                 f"{', '.join(p.split(':', 1)[1] for p in d['peers'])} have (both reach {d['via']})")
    miss = [i for i in res["items"] if i["severity"] == "missing" and not i["covered"]]
    rev = [i for i in res["items"] if i["severity"] == "review" and not i["covered"]]
    o += ["", f"2 MISSING FROM PLAN ({len(miss)}): affected by the planned change, not covered by any plan entry"]
    o += [_fmt_item(i) for i in miss[:max_items]]
    o.append(f"  REVIEW ({len(rev)}): related; confirm unaffected or add to covers/out_of_scope")
    o += [_fmt_item(i) for i in rev[:max_items]]
    if len(rev) > max_items:
        o.append(f"    ... {len(rev) - max_items} more (see --json)")
    if show_covered:
        cv = [i for i in res["items"] if i["covered"]]
        cvn = sorted({short(i['node']) for i in cv})
        o.append(f"  COVERED by plan ({len(cv)} items, {len(cvn)} nodes): " + ", ".join(cvn))
    for p in res.get("precedents") or []:
        o.append(f"  PRECEDENT {p['name']} /{p['pattern']}/{' for ' + p['for'] if p.get('for') else ''}: {len(p['hits'])} hit(s)")
        o += [f"    {short(h['node'])} @{bloc(h['at'])}: {h['text']}" for h in p["hits"][:4]]
    o += ["", "3 CONFLICTS"]
    for c in res["conflicts"]:
        g = c.get("guard")
        gs = (f"; guard ({g['at']} {g['needs']}): " + ("PRESENT " + ", ".join(bloc(e) for e in g["evidence"]) if g["present"] else "ABSENT")) if g else ""
        o.append(f"  forbid {c['id']}: path {'STILL PRESENT' if c['present'] else 'absent'}" + (f" (when {c['when']})" if c.get("when") else "") + gs)
        if c.get("chain"):
            o.append(f"    {c['chain']}")
    for f in res["findings"]:
        if not f["touches"]:
            continue
        tag = "linked in plan" if f["linked"] else ("NOT LINKED in plan" if f["touches_plan"] else "touches gaps only")
        o.append(f"  {f['id']} [{f['state']}, {tag}] {f['title']}")
        o.append("    " + "; ".join(f"{short(k)}{'*' if k in f['touches_plan'] else ''} {','.join(v)}" for k, v in list(f["touches"].items())[:7]))
    if res.get("findings"):
        o.append("    (* = node the plan modifies/references)")
    if res.get("verify"):
        v = res["verify"]
        o += ["", f"4 VERIFY implementation vs plan: {'OK' if s.get('verify_ok') else 'INCOMPLETE'}"]
        o += [f"  node {x['id']}: {'exists @' + bloc(x['at']) if x['ok'] else 'MISSING'}" for x in v["nodes"]]
        o += [f"  edge {x['edge']}: {'exists ' + ', '.join(x['evidence']) if x['ok'] else 'MISSING'}" for x in v["edges"]]
        o += [f"  modified {short(x['target'])}: {x['state']}" for x in v["modified"]]
        for x in v["forbid"]:
            o.append(f"  forbid {x['id']}: {'ok' if x['ok'] else 'VIOLATED'} (path {'present' if x['present'] else 'gone'}"
                     + (f", guard {'present ' + ', '.join(bloc(e) for e in x['guard']['evidence']) if x['guard']['present'] else 'absent'}" if x.get("guard") else "") + ")")
        o += [f"  require {x['route'].split(':', 1)[1]}: {'ok' if x['ok'] else 'MISSING ' + ', '.join(x['missing'])}" for x in v["require"]]
    chains = [i["chain"] for i in res["items"] if i["check"] == "entry_point" and i.get("chain")]
    if chains:
        o += ["", "ENTRY CHAINS (entry point -> modified code)"] + [f"  {c}" for c in dict.fromkeys(chains)]
    return "\n".join(o)


def render_check_summary(res: dict, max_items: int = 5) -> str:
    """Compact plan check: counts per section and per check, the top missing items (one line each), failed
    requirements, middleware gaps, forbidden paths, open findings and verify counts. The full report is render_check."""
    s = res["summary"]
    o = [f"PLAN CHECK {res['plan']} [{res['mode']} mode] {res['title']}",
         f"plan {res['file']} | graph {res['graph'].get('project')} indexed {res['graph'].get('indexed_at')}",
         f"summary: refs {s['references'] - s['unresolved']}/{s['references']} resolve | MISSING FROM PLAN {s['missing_from_plan']} | "
         f"review {s['review']} | covered {s['covered']} | forbidden paths present {s['forbidden_paths_present']} | "
         f"open findings touching {s['open_findings_touching']} (unlinked {s['unlinked_open_findings']}) | requirements failed {s['requirements_failed']}"
         + (f" | verify {s['verify']}" if s.get("verify") else "")]
    o += [f"SCHEMA {e}" for e in res["schema_errors"]]
    bad = [r for r in res["resolve"]["refs"] if r["status"] in ("unresolved", "ambiguous")]
    o += [f"  {r['status'].upper()} {r['where']}: {r['spec']}" for r in bad[:max_items]]
    for r in res.get("require") or []:
        if r["missing"]:
            o.append(f"require {r['route'].split(':', 1)[1]}: MISSING {', '.join(r['missing'])} (has {', '.join(r['middleware']) or 'none'} @{bloc(r['at'])})")
    for rid, d in (res.get("middleware_diff") or {}).items():
        o.append(f"middleware: {rid.split(':', 1)[1]} lacks {', '.join(d['lacks'])} which peer route(s) "
                 f"{', '.join(p.split(':', 1)[1] for p in d['peers'])} have")
    miss = [i for i in res["items"] if i["severity"] == "missing" and not i["covered"]]
    rev = [i for i in res["items"] if i["severity"] == "review" and not i["covered"]]

    def by_check(items):
        c = defaultdict(int)
        for i in items:
            c[i["check"]] += 1
        return ", ".join(f"{k} {v}" for k, v in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))) or "-"
    o += ["", f"2 MISSING FROM PLAN ({len(miss)}) by check: {by_check(miss)}"]
    for i in miss[:max_items]:
        o.append(f"  - [{i['check']}] {short(i['node'])}: {i['why']} @ {(i['evidence'] or ['-'])[0]}")
    if len(miss) > max_items:
        o.append(f"  … +{len(miss) - max_items} more")
    o.append(f"  REVIEW ({len(rev)}) by check: {by_check(rev)}")
    o += ["", "3 CONFLICTS"]
    for c in res["conflicts"]:
        o.append(f"  forbid {c['id']}: path {'STILL PRESENT' if c['present'] else 'absent'}" + (f" (when {c['when']})" if c.get("when") else ""))
    for f in res["findings"]:
        if f["touches"]:
            tag = "linked in plan" if f["linked"] else ("NOT LINKED in plan" if f["touches_plan"] else "touches gaps only")
            o.append(f"  {f['id']} [{f['state']}, {tag}] {f['title']}")
    if res.get("verify"):
        v = res["verify"]
        o += ["", f"4 VERIFY implementation vs plan: {'OK' if s.get('verify_ok') else 'INCOMPLETE'} "
                  f"({', '.join(f'{k} {x}' for k, x in s['verify'].items())})"]
        o += [f"  NOT MET {k[:-1] if k.endswith('s') else k}: {x.get('id') or x.get('edge') or x.get('target') or x.get('route')}"
              for k in ("nodes", "edges", "forbid", "require") for x in v[k] if not x["ok"]][:max_items]
    o += ["", "details=true for every item with file:line evidence and call chains (CLI: plan check without --summary); "
              "max_items=N shows more top items."]
    return "\n".join(o)

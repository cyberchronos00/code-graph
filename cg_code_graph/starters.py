"""Starter queries derived from the indexed graph, so the visual view and MCP offer useful first questions on any
repository: write routes without an auth guard, the most-reached tables / connections / env keys, the page with the
largest backend reach and the most-called functions. Every starter is checked to resolve to existing nodes.

Each starter: {id, label, mode, specs[, sinks], why, cli, mcp} with mode one of reaches / impact / downstream (the
visual view's modes); `cli` and `mcp` are the matching command and tool call."""
from __future__ import annotations

import json
import sqlite3
import time
from collections import deque

from . import query as Q
from .core.model import PROPAGATING
from .core.store import GraphStore

READS = ("READS_TABLE", "READS_COLUMN")
WRITES = ("WRITES_TABLE", "WRITES_COLUMN")
CODE_KINDS = ("method", "function")


def _label(nid: str) -> str:
    return Q.short_id(nid)


def _table_of(dst: str) -> str:
    return "table:" + dst.split(":", 1)[1].split(".")[0]


def _top_tables(st: GraphStore, kinds: tuple[str, ...], n: int = 2) -> list[tuple[str, int]]:
    rows = st.q(f"SELECT src, dst FROM edges WHERE kind IN ({','.join('?' * len(kinds))})", kinds)
    users: dict[str, set] = {}
    for r in rows:
        users.setdefault(_table_of(r["dst"]), set()).add(r["src"])
    ranked = sorted(((t, len(s)) for t, s in users.items()), key=lambda x: (-x[1], x[0]))
    return [(t, c) for t, c in ranked if st.q("SELECT 1 FROM nodes WHERE id=?", (t,))][:n]


def _top_by_dst(st: GraphStore, kind: str, prefix: str) -> tuple[str, int] | None:
    r = st.q("SELECT dst, count(DISTINCT src) c FROM edges WHERE kind=? AND dst LIKE ? GROUP BY dst ORDER BY c DESC, dst LIMIT 1",
             (kind, prefix + "%"))
    return (r[0]["dst"], r[0]["c"]) if r and r[0]["c"] else None


def _most_called(st: GraphStore, n: int = 2) -> list[tuple[str, int]]:
    rows = st.q("""SELECT e.dst, count(DISTINCT e.src) c FROM edges e JOIN nodes d ON d.id = e.dst
                   WHERE e.kind='CALLS' AND d.kind IN ('method','function') AND coalesce(json_extract(d.attrs,'$.placeholder'),0)=0
                   GROUP BY e.dst ORDER BY c DESC, e.dst LIMIT ?""", (n,))
    return [(r["dst"], r["c"]) for r in rows if r["c"] > 1]


def _deepest_page(st: GraphStore, cap: int = 4000, deadline: float | None = None) -> tuple[str, int, int] | None:
    """The page whose forward closure reaches the most routes / tables (then nodes): (page id, sinks, nodes). A page
    that reaches no route or table is not offered."""
    pages = [r["id"] for r in st.q("SELECT id FROM nodes WHERE kind='page' ORDER BY id LIMIT 400")]
    if not pages:
        return None
    prop = set(PROPAGATING)
    fwd: dict[str, list[str]] = {}
    for e in st.q("SELECT src, dst, kind FROM edges"):
        if e["kind"] in prop:
            fwd.setdefault(e["src"], []).append(e["dst"])
    best = None
    for p in pages:
        if deadline is not None and time.time() > deadline:
            raise Q.Deadline()
        seen, dq = {p}, deque([p])
        while dq and len(seen) < cap:
            for y in fwd.get(dq.popleft(), ()):
                if y not in seen:
                    seen.add(y)
                    dq.append(y)
        sinks = sum(1 for x in seen if x.startswith(("route:", "table:", "column:", "http:")))
        key = (sinks, len(seen))
        if len(seen) > 1 and (best is None or key > best[1:]):
            best = (p, *key)
    return best


def _unguarded_writes(st: GraphStore, deadline: float | None = None) -> tuple[str, int, int] | None:
    """(route id, tables it writes, unguarded write routes) for the unguarded route writing the most tables."""
    from .routes import routes_report
    res = routes_report(st, writes="*", unguarded=True, deadline=deadline)
    if not res["items"]:
        return None
    def nt(i):
        return len({r["what"] for r in i["reaches"]})
    top = max(res["items"], key=lambda i: (nt(i), i["name"]))
    return top["route"], nt(top), len(res["items"])


def _within(st: GraphStore, deadline: float, fn):
    """fn() with SQLite queries and traversals stopped at `deadline` (query.Deadline)."""
    st.db.set_progress_handler(lambda: 1 if time.time() > deadline else 0, 20000)
    try:
        return fn()
    except sqlite3.OperationalError as ex:
        if "interrupt" in str(ex) and time.time() > deadline:
            raise Q.Deadline() from ex
        raise
    finally:
        st.db.set_progress_handler(None, 0)


def generate(st: GraphStore, budget_s: float = 20.0, report: dict | None = None) -> list[dict]:
    """Starter queries for this graph, each verified to resolve to existing nodes. The whole run stays within
    `budget_s`: a starter that is not done by then is skipped and listed in report["skipped"] (stats.starters_skipped)."""
    t0 = time.time()
    deadline = t0 + budget_s
    out: list[dict] = []
    skipped: list[str] = []
    if report is not None:
        report["skipped"] = skipped

    def add(sid, label, mode, specs, why, cli, mcp, sinks=None):
        if not all(Q.resolve_targets(st, s) for s in specs):
            return
        e = {"id": sid, "label": label, "mode": mode, "specs": specs, "why": why, "cli": cli, "mcp": mcp, "starter": True}
        if sinks:
            e["sinks"] = sinks
        out.append(e)

    def step(sid, fn):
        """Run one starter within the remaining budget; True when it ran to the end."""
        if time.time() > deadline:
            skipped.append(sid)
            return False
        n = len(out)
        try:
            _within(st, deadline, fn)
            return True
        except Q.Deadline:
            del out[n:]
            skipped.append(sid)
            return False

    n_routes = st.q("SELECT count(*) c FROM nodes WHERE kind='route'")[0]["c"]
    has_writes = bool(st.q("SELECT 1 FROM edges WHERE kind IN ('WRITES_TABLE','WRITES_COLUMN') LIMIT 1"))

    def unguarded():
        u = _unguarded_writes(st, deadline)
        if u:
            rid, nt, total = u
            add("starter_unguarded_write", f"write route without an auth guard: {_label(rid)} -> what it writes "
                f"({total} unguarded write route{'s' if total != 1 else ''})", "downstream", [rid],
                f"{total} route(s) reach a DB write and have no auth-like guard; this one writes {nt} table(s)",
                "cg routes --writes --unguarded --db DB", "routes(writes='*', unguarded=true)", sinks=["table", "column"])

    def most_written():
        for t, c in _top_tables(st, WRITES, 1):
            add("starter_most_written_table", f"who writes {t[6:]} (most-written table, {c} writer{'s' if c != 1 else ''})", "reaches", [t],
                f"{c} functions write it", f"cg writers {t[6:]} --db DB", f"writers('{t[6:]}')")

    def most_read():
        for t, c in _top_tables(st, READS, 1):
            if not any(s["specs"] == [t] for s in out):
                add("starter_most_read_table", f"what depends on {t[6:]} (most-read table, {c} reader{'s' if c != 1 else ''})", "reaches", [t],
                    f"{c} functions read it", f"cg reaches {t} --db DB", f"reaches(['{t}'])")

    def top_connection():
        con = _top_by_dst(st, "USES_CONNECTION", "connection:")
        if con:
            add("starter_top_connection", f"what reaches {con[0]} (most-used DB connection)", "reaches", [con[0]],
                f"{con[1]} code paths use it", f"cg reaches {con[0]} --db DB", f"reaches(['{con[0]}'])")

    def top_env():
        env = _top_by_dst(st, "READS_ENV", "env:")
        if env:
            add("starter_top_env", f"what depends on {env[0]} (most-read env key)", "reaches", [env[0]],
                f"read in {env[1]} places", f"cg reaches {env[0]} --db DB", f"reaches(['{env[0]}'])")

    def deepest_page():
        pg = _deepest_page(st, deadline=deadline)
        if pg and pg[1]:
            add("starter_deepest_page", f"{_label(pg[0])} -> backend (the page with the largest reach: {pg[1]} routes / tables)",
                "downstream", [pg[0]], f"its forward closure has {pg[2]} nodes", f"cg downstream '{pg[0]}' --db DB",
                f"downstream('{pg[0]}')", sinks=["route", "table", "column"])

    def most_called():
        for i, (fn, c) in enumerate(_most_called(st, 2)):
            add(f"starter_most_called_{i + 1}", f"impact of {_label(fn)} ({c} direct callers)", "impact", [fn],
                f"called from {c} functions", f"cg impact '{fn}' --db DB", f"impact('{fn}')")

    if n_routes and has_writes:
        step("starter_unguarded_write", unguarded)
    step("starter_most_written_table", most_written)
    step("starter_most_read_table", most_read)
    step("starter_top_connection", top_connection)
    step("starter_top_env", top_env)
    step("starter_deepest_page", deepest_page)
    step("starter_most_called", most_called)
    return out


def for_graph(st: GraphStore) -> list[dict]:
    """Starters recorded at index time (stats.starters; a combined graph: per repo, prefixed), else generated now."""
    try:
        m = st.meta()
    except Exception:  # noqa: BLE001
        return []
    s = (m.get("stats") or {}).get("starters")
    if isinstance(s, list) and not m.get("repos"):
        return s
    return generate(st)


def render(starters: list[dict]) -> str:
    if not starters:
        return "no starter queries: the graph has no routes, tables, connections, env keys, pages or shared functions yet"
    out = [f"starter queries ({len(starters)}), derived from this graph:"]
    for s in starters:
        out.append(f"- {s['label']}\n    MCP: {s['mcp']}   CLI: {s['cli']}")
    return "\n".join(out)


def dumps(starters: list[dict]) -> str:
    return json.dumps(starters, indent=1)
